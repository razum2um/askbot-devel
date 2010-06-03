import datetime
#todo: maybe eliminate usage of django.settings
from django.conf import settings
from forum.conf import settings as forum_settings
from django.utils import simplejson
from django.http import HttpResponse, HttpResponseRedirect
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, render_to_response
from django.utils.translation import ugettext as _
from django.template import RequestContext
from forum.models import *
from forum.forms import CloseForm
from forum import auth
from django.core.urlresolvers import reverse
from django.contrib.auth.decorators import login_required
from forum.utils.decorators import ajax_method, ajax_login_required
import logging

def vote(request, id):#todo: pretty incomprehensible view used by various ajax calls
#issues: this subroutine is too long, contains many magic numbers and other issues
#it's called "vote" but many actions processed here have nothing to do with voting
    """
    vote_type:
        acceptAnswer : 0,
        questionUpVote : 1,
        questionDownVote : 2,
        favorite : 4,
        answerUpVote: 5,
        answerDownVote:6,
        offensiveQuestion : 7,
        offensiveAnswer:8,
        removeQuestion: 9,
        removeAnswer:10
        questionSubscribeUpdates:11
        questionUnSubscribeUpdates:12

    accept answer code:
        response_data['allowed'] = -1, Accept his own answer   0, no allowed - Anonymous    1, Allowed - by default
        response_data['success'] =  0, failed                                               1, Success - by default
        response_data['status']  =  0, By default                                           1, Answer has been accepted already(Cancel)

    vote code:
        allowed = -3, Don't have enough votes left
                  -2, Don't have enough reputation score
                  -1, Vote his own post
                   0, no allowed - Anonymous
                   1, Allowed - by default
        status  =  0, By default
                   1, Cancel
                   2, Vote is too old to be canceled

    offensive code:
        allowed = -3, Don't have enough flags left
                  -2, Don't have enough reputation score to do this
                   0, not allowed
                   1, allowed
        status  =  0, by default
                   1, can't do it again
    """
    response_data = {
        "allowed": 1,
        "success": 1,
        "status" : 0,
        "count"  : 0,
        "message" : ''
    }

    def __can_vote(vote_score, user):#refactor - belongs to auth.py
        if vote_score == 1:#refactor magic number
            return auth.can_vote_up(request.user)
        else:
            return auth.can_vote_down(request.user)

    try:
        if not request.user.is_authenticated():
            response_data['allowed'] = 0
            response_data['success'] = 0

        elif request.is_ajax() and request.method == 'POST':
            question = get_object_or_404(Question, id=id)
            vote_type = request.POST.get('type')

            #accept answer
            if vote_type == '0':
                answer_id = request.POST.get('postId')
                answer = get_object_or_404(Answer, id=answer_id)
                # make sure question author is current user
                if question.author == request.user:
                    # answer user who is also question author is not allow to accept answer
                    if answer.author == question.author:
                        response_data['success'] = 0
                        response_data['allowed'] = -1
                    # check if answer has been accepted already
                    elif answer.accepted:
                        auth.onAnswerAcceptCanceled(answer, request.user)
                        response_data['status'] = 1
                    else:
                        # set other answers in this question not accepted first
                        for answer_of_question in Answer.objects.get_answers_from_question(question, request.user):
                            if answer_of_question != answer and answer_of_question.accepted:
                                auth.onAnswerAcceptCanceled(answer_of_question, request.user)

                        #make sure retrieve data again after above author changes, they may have related data
                        answer = get_object_or_404(Answer, id=answer_id)
                        auth.onAnswerAccept(answer, request.user)
                else:
                    response_data['allowed'] = 0
                    response_data['success'] = 0
            # favorite
            elif vote_type == '4':
                has_favorited = False
                fave = request.user.toggle_favorite_question(question)
                response_data['count'] = FavoriteQuestion.objects.filter(
                                            question = question
                                        ).count()
                if fave == False:
                    response_data['status'] = 1

            elif vote_type in ['1', '2', '5', '6']:
                post_id = id
                post = question
                vote_score = 1
                if vote_type in ['5', '6']:
                    answer_id = request.POST.get('postId')
                    answer = get_object_or_404(Answer, id=answer_id)
                    post_id = answer_id
                    post = answer
                if vote_type in ['2', '6']:
                    vote_score = -1

                if post.author == request.user:
                    response_data['allowed'] = -1
                elif not __can_vote(vote_score, request.user):
                    response_data['allowed'] = -2
                elif post.votes.filter(user=request.user).count() > 0:
                    #todo: I think we have a bug here
                    #we need to instead select vote on that particular post
                    #not just the latest vote, although it is a good shortcut.
                    #The problem is that this vote is deleted in one of
                    #the on...Canceled() functions
                    vote = post.votes.filter(user=request.user)[0]
                    # get latest vote by the current user
                    # unvote should be less than certain time
                    if (datetime.datetime.now().day - vote.voted_at.day) \
                        >= forum_settings.MAX_DAYS_TO_CANCEL_VOTE:
                        response_data['status'] = 2
                    else:
                        voted = vote.vote
                        if voted > 0:
                            # cancel upvote
                            auth.onUpVotedCanceled(vote, post, request.user)

                        else:
                            # cancel downvote
                            auth.onDownVotedCanceled(vote, post, request.user)

                        response_data['status'] = 1
                        response_data['count'] = post.score
                elif Vote.objects.get_votes_count_today_from_user(request.user)\
                >= forum_settings.MAX_VOTES_PER_USER_PER_DAY:
                    response_data['allowed'] = -3
                else:
                    vote = Vote(user=request.user, content_object=post, vote=vote_score, voted_at=datetime.datetime.now())
                    if vote_score > 0:
                        # upvote
                        auth.onUpVoted(vote, post, request.user)
                    else:
                        # downvote
                        auth.onDownVoted(vote, post, request.user)

                    votes_left = forum_settings.MAX_VOTES_PER_USER_PER_DAY \
                    - Vote.objects.get_votes_count_today_from_user(request.user)
                    if votes_left <= \
                            forum_settings.VOTES_LEFT_WARNING_THRESHOLD:
                        response_data['message'] = u'%s votes left' % votes_left
                    response_data['count'] = post.score
            elif vote_type in ['7', '8']:
                post = question
                post_id = id
                if vote_type == '8':
                    post_id = request.POST.get('postId')
                    post = get_object_or_404(Answer, id=post_id)

                if FlaggedItem.objects.get_flagged_items_count_today(request.user) >= forum_settings.MAX_FLAGS_PER_USER_PER_DAY:
                    response_data['allowed'] = -3
                elif not auth.can_flag_offensive(request.user):
                    response_data['allowed'] = -2
                elif post.flagged_items.filter(user=request.user).count() > 0:
                    response_data['status'] = 1
                else:
                    item = FlaggedItem(user=request.user, content_object=post, flagged_at=datetime.datetime.now())
                    auth.onFlaggedItem(item, post, request.user)
                    response_data['count'] = post.offensive_flag_count
            elif vote_type in ['9', '10']:
                post = question
                post_id = id
                if vote_type == '10':
                    post_id = request.POST.get('postId')
                    post = get_object_or_404(Answer, id=post_id)

                if not auth.can_delete_post(request.user, post):
                    response_data['allowed'] = -2
                elif post.deleted == True:
                    logging.debug('debug restoring post in view')
                    auth.onDeleteCanceled(post, request.user)
                    response_data['status'] = 1
                else:
                    auth.onDeleted(post, request.user)
            elif vote_type == '11':#subscribe q updates
                user = request.user
                if user.is_authenticated():
                    if user not in question.followed_by.all():
                        question.followed_by.add(user)
                        if forum_settings.EMAIL_VALIDATION == True \
                            and user.email_isvalid == False:

                            response_data['message'] = \
                                    _('subscription saved, %(email)s needs validation, see %(details_url)s') \
                                    % {'email':user.email,'details_url':reverse('faq') + '#validate'}
                    feed_setting = EmailFeedSetting.objects.get(subscriber=user,feed_type='q_sel')
                    if feed_setting.frequency == 'n':
                        feed_setting.frequency = 'd'
                        feed_setting.save()
                        if 'message' in response_data:
                            response_data['message'] += '<br/>'
                        response_data['message'] = _('email update frequency has been set to daily')
                    #response_data['status'] = 1
                    #responst_data['allowed'] = 1
                else:
                    pass
                    #response_data['status'] = 0
                    #response_data['allowed'] = 0
            elif vote_type == '12':#unsubscribe q updates
                user = request.user
                if user.is_authenticated():
                    if user in question.followed_by.all():
                        question.followed_by.remove(user)
        else:
            response_data['success'] = 0
            response_data['message'] = u'Request mode is not supported. Please try again.'

        data = simplejson.dumps(response_data)

    except Exception, e:
        response_data['message'] = str(e)
        data = simplejson.dumps(response_data)
    return HttpResponse(data, mimetype="application/json")

#internally grouped views - used by the tagging system
@ajax_login_required
def mark_tag(request, tag=None, **kwargs):#tagging system
    action = kwargs['action']
    ts = MarkedTag.objects.filter(user=request.user, tag__name=tag)
    if action == 'remove':
        logging.debug('deleting tag %s' % tag)
        ts.delete()
    else:
        reason = kwargs['reason']
        if len(ts) == 0:
            try:
                t = Tag.objects.get(name=tag)
                mt = MarkedTag(user=request.user, reason=reason, tag=t)
                mt.save()
            except:
                pass
        else:
            ts.update(reason=reason)
    return HttpResponse(simplejson.dumps(''), mimetype="application/json")

@ajax_login_required
def ajax_toggle_ignored_questions(request):#ajax tagging and tag-filtering system
    if request.user.hide_ignored_questions:
        new_hide_setting = False
    else:
        new_hide_setting = True
    request.user.hide_ignored_questions = new_hide_setting
    request.user.save()

@ajax_method
def ajax_command(request):
    """view processing ajax commands - note "vote" and view others do it too
    """
    if 'command' not in request.POST:
        return HttpResponseForbidden(mimetype="application/json")
    if request.POST['command'] == 'toggle-ignored-questions':
        return ajax_toggle_ignored_questions(request)

@login_required
def close(request, id):#close question
    """view to initiate and process 
    question close
    """
    question = get_object_or_404(Question, id=id)
    if not auth.can_close_question(request.user, question):
        return HttpResponse('Permission denied.')
    if request.method == 'POST':
        form = CloseForm(request.POST)
        if form.is_valid():
            reason = form.cleaned_data['reason']
            question.closed = True
            question.closed_by = request.user
            question.closed_at = datetime.datetime.now()
            question.close_reason = reason
            question.save()
        return HttpResponseRedirect(question.get_absolute_url())
    else:
        form = CloseForm()
        return render_to_response('close.html', {
            'form' : form,
            'question' : question,
            }, context_instance=RequestContext(request))

@login_required
def reopen(request, id):#re-open question
    """view to initiate and process 
    question close
    """
    question = get_object_or_404(Question, id=id)
    # open question
    if not auth.can_reopen_question(request.user, question):
        return HttpResponse('Permission denied.')
    if request.method == 'POST' :
        Question.objects.filter(id=question.id).update(closed=False,
            closed_by=None, closed_at=None, close_reason=None)
        return HttpResponseRedirect(question.get_absolute_url())
    else:
        return render_to_response('reopen.html', {
            'question' : question,
            }, context_instance=RequestContext(request))

#askbot-user communication system
def read_message(request):#marks message a read
    if request.method == "POST":
        if request.POST['formdata'] == 'required':
            request.session['message_silent'] = 1
            if request.user.is_authenticated():
                request.user.delete_messages()
    return HttpResponse('')
